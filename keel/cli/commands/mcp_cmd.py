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

    server = create_server()
    server.run(transport="stdio")
