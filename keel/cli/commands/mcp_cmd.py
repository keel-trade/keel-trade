"""MCP server command — start the Keel MCP server."""

from __future__ import annotations

import click


def _log_credential_source() -> None:
    """One stderr line naming the credential this server acts as (Q-2579).

    stdout is the MCP stream, so it never goes there. No network call: the
    org is `keel_account_status` / `keel doctor`."""
    import sys

    from keel.config import credential_source_label, load_config

    try:
        config = load_config()
    except Exception:  # noqa: BLE001 — a log line never stops the server
        return
    label = credential_source_label(config) or "none (sign in with keel_auth_login)"
    note = "; the stored login is not used" if config.credential_source == "env" else ""
    print(f"keel mcp serve: credential = {label} -> {config.api_url}{note}", file=sys.stderr)


@click.group()
def mcp() -> None:
    """MCP server for IDE integration."""


@mcp.command()
def serve() -> None:
    """Start the MCP server.

    Agent hosts launch this command as a local stdio child process.
    """
    from keel.mcp.server import create_server
    from keel.surface import set_surface

    # Surface self-identification (spec 08 R5): local stdio MCP — not the
    # CLI (the parent `cli()` group already set "cli"; override).
    set_surface("local-mcp")
    _log_credential_source()
    server = create_server()
    server.run(transport="stdio")
