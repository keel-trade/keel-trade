"""MCP server command — start the Keel MCP server."""

from __future__ import annotations

import click


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
    server = create_server()
    server.run(transport="stdio")
