"""`keel context` — manage the 3-layer user context.

Per spec §9.4 (lines 1178-1192).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from keel.cli.main import _get_format
from keel.context import (
    append_user_context,
    home_context_path,
    init_user_context,
    read_project_context,
    read_user_context,
    write_user_context,
)
from keel.output import emit, emit_error


@click.group()
def context() -> None:
    """Manage user / project context (read by the MCP server on session start)."""


@context.command("init")
@click.option("--force", is_flag=True, help="Overwrite an existing ~/.keel/context.md")
@click.option("--user", default="you", help="Identity hint for the template header.")
@click.pass_context
def context_init(ctx: click.Context, force: bool, user: str) -> None:
    """Write the 6-section template to ~/.keel/context.md."""
    try:
        path = init_user_context(user=user, overwrite=force)
    except FileExistsError as e:
        emit_error(str(e), _get_format(ctx))
        ctx.exit(5)
        return
    emit(
        {
            "path": str(path),
            "next_steps": [
                f"keel context edit             # fill in the template",
                f"keel context show --layer user",
            ],
        },
        _get_format(ctx),
    )


@context.command("show")
@click.option(
    "--layer",
    type=click.Choice(["user", "project"]),
    default="user",
    show_default=True,
    help="Which context layer to print. Strategy-layer comes from `keel strategy memory-read`.",
)
@click.pass_context
def context_show(ctx: click.Context, layer: str) -> None:
    """Print the contents of the requested context layer."""
    entry = read_user_context() if layer == "user" else read_project_context()
    payload = {
        "layer": entry.layer,
        "source": str(entry.source) if entry.source else None,
        "exists": entry.exists,
        "body": entry.body,
    }
    emit(payload, _get_format(ctx))


@context.command("add")
@click.argument("note")
@click.pass_context
def context_add(ctx: click.Context, note: str) -> None:
    """Append a timestamped note to ~/.keel/context.md's ## Notes section."""
    path = append_user_context(note)
    emit({"path": str(path), "appended": note}, _get_format(ctx))


@context.command("import")
@click.argument("source")
@click.pass_context
def context_import(ctx: click.Context, source: str) -> None:
    """Replace ~/.keel/context.md with the contents of a file or URL."""
    if source.startswith(("http://", "https://")):
        import httpx

        try:
            resp = httpx.get(source, timeout=10.0)
            resp.raise_for_status()
            body = resp.text
        except Exception as e:  # noqa: BLE001
            emit_error(f"Failed to fetch {source}: {e}", _get_format(ctx))
            ctx.exit(1)
            return
    else:
        path = Path(source).expanduser()
        if not path.exists():
            emit_error(f"File not found: {path}", _get_format(ctx))
            ctx.exit(3)
            return
        body = path.read_text(encoding="utf-8")

    out_path = write_user_context(body)
    emit({"path": str(out_path), "bytes": len(body)}, _get_format(ctx))


@context.command("edit")
@click.pass_context
def context_edit(ctx: click.Context) -> None:
    """Open ~/.keel/context.md in $EDITOR (defaults to vi)."""
    path = home_context_path()
    if not path.exists():
        # Initialise first so users always have something to edit.
        init_user_context()

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    try:
        subprocess.run([editor, str(path)], check=True)
    except FileNotFoundError:
        emit_error(
            f"Editor {editor!r} not found. Set $EDITOR to a command on your PATH.",
            _get_format(ctx),
        )
        ctx.exit(1)
    except subprocess.CalledProcessError as e:
        emit_error(f"Editor exited non-zero: {e}", _get_format(ctx))
        ctx.exit(e.returncode or 1)
