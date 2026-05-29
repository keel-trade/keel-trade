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
    return {
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
@click.pass_context
def login(
    ctx: click.Context,
    key: str | None,
    scope: str,
    api_url: str | None,
) -> None:
    """Log in to Keel.

    Default: opens a browser, runs the OAuth flow, persists tokens locally.
    For CI / SSH / remote dev environments: use --key with a token from
    https://app.usekeel.io/settings?tab=api-keys.
    """
    from keel.auth import browser_login

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
                        "No API key on stdin. In agent mode, pipe the key in or "
                        "pass --key <token>."
                    ),
                },
                _get_format(ctx),
            )
            ctx.exit(2)
            return
        _do_api_key_login(ctx, stdin_key, api_url)
        return

    # Interactive default: browser-OAuth loopback flow.
    try:
        info = browser_login(
            api_url=api_url,
            include_live=(scope == "live"),
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
