"""Universe management commands — 5 local + 3 remote tools."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from keel.cli.main import _get_format
from keel.output import emit, emit_error


def _read_source(file_arg: str) -> str:
    """Read source from file, stdin, or auto-detected workspace."""
    if file_arg != "-":
        p = Path(file_arg)
        if not p.exists():
            raise click.BadParameter(f"File not found: {file_arg}")
        return p.read_text()

    if not sys.stdin.isatty():
        content = sys.stdin.read()
        if content.strip():
            return content

    from keel.workspace import find_workspace_strategy, read_local_source

    ws = find_workspace_strategy()
    if ws:
        return read_local_source(ws.strategy_id)

    raise click.UsageError("No file, stdin, or workspace strategy found.")


def _write_back_if_workspace(file_arg: str, new_source: str) -> bool:
    """If the source came from a workspace, write the new source back.

    Returns True if written back to workspace, False otherwise.
    """
    if file_arg != "-":
        # Explicit file — write back to that file
        Path(file_arg).write_text(new_source)
        return True

    if not sys.stdin.isatty():
        return False  # Piped input — can't write back

    from keel.workspace import find_workspace_strategy, write_local_source

    ws = find_workspace_strategy()
    if ws:
        write_local_source(ws.strategy_id, new_source)
        return True

    return False


@click.group()
def universe() -> None:
    """Manage strategy universe (asset selection)."""


@universe.command("set")
@click.argument("file")
@click.option("--mode", required=True, type=click.Choice(["manual", "category", "top_volume"]))
@click.option("--market", default="perp")
@click.option("--symbols", multiple=True, help="Symbol list (manual mode)")
@click.option("--categories", multiple=True, help="Category tags (category mode)")
@click.option("--top-n", type=int, help="Number of assets (top_volume mode)")
@click.option("--exclusions", multiple=True)
@click.option("--inclusions", multiple=True)
@click.pass_context
def set_universe(
    ctx: click.Context,
    file: str,
    mode: str,
    market: str,
    symbols: tuple[str, ...],
    categories: tuple[str, ...],
    top_n: int | None,
    exclusions: tuple[str, ...],
    inclusions: tuple[str, ...],
) -> None:
    """Set or replace universe criteria on a strategy."""
    from keel.tools.local import universe_set

    try:
        source = _read_source(file)
        kwargs: dict = {"source": source, "mode": mode, "market": market}
        if symbols:
            kwargs["symbols"] = list(symbols)
        if categories:
            kwargs["categories"] = list(categories)
        if top_n is not None:
            kwargs["top_n"] = top_n
        if exclusions:
            kwargs["exclusions"] = list(exclusions)
        if inclusions:
            kwargs["inclusions"] = list(inclusions)
        result = universe_set(**kwargs)
        # Write back modified source to file/workspace
        if "source" in result:
            _write_back_if_workspace(file, result["source"])
        emit(result, _get_format(ctx))
    except click.BadParameter:
        raise
    except ValueError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(7)
    except Exception as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(1)


@universe.command("get")
@click.argument("file")
@click.pass_context
def get_universe(ctx: click.Context, file: str) -> None:
    """Read universe configuration from a strategy."""
    from keel.tools.local import universe_get

    try:
        source = _read_source(file)
        result = universe_get(source=source)
        emit(result, _get_format(ctx))
    except click.BadParameter:
        raise
    except Exception as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(1)


@universe.command("add-group")
@click.argument("file")
@click.argument("name")
@click.option("--symbols", multiple=True, help="Symbols in this group")
@click.pass_context
def add_group(ctx: click.Context, file: str, name: str, symbols: tuple[str, ...]) -> None:
    """Add a named group to the universe."""
    from keel.tools.local import universe_add_group

    try:
        source = _read_source(file)
        kwargs: dict = {"source": source, "name": name}
        if symbols:
            kwargs["symbols"] = list(symbols)
        result = universe_add_group(**kwargs)
        if "source" in result:
            _write_back_if_workspace(file, result["source"])
        emit(result, _get_format(ctx))
    except click.BadParameter:
        raise
    except ValueError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(7)
    except Exception as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(1)


@universe.command("modify-group")
@click.argument("file")
@click.argument("name")
@click.option("--add", multiple=True, help="Symbols to add")
@click.option("--remove", multiple=True, help="Symbols to remove")
@click.pass_context
def modify_group(
    ctx: click.Context, file: str, name: str, add: tuple[str, ...], remove: tuple[str, ...]
) -> None:
    """Modify an existing universe group."""
    from keel.tools.local import universe_modify_group

    try:
        source = _read_source(file)
        kwargs: dict = {"source": source, "name": name}
        if add:
            kwargs["add"] = list(add)
        if remove:
            kwargs["remove"] = list(remove)
        result = universe_modify_group(**kwargs)
        if "source" in result:
            _write_back_if_workspace(file, result["source"])
        emit(result, _get_format(ctx))
    except click.BadParameter:
        raise
    except ValueError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(7)
    except Exception as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(1)


@universe.command("remove-group")
@click.argument("file")
@click.argument("name")
@click.pass_context
def remove_group(ctx: click.Context, file: str, name: str) -> None:
    """Remove a named group from the universe."""
    from keel.tools.local import universe_remove_group

    try:
        source = _read_source(file)
        result = universe_remove_group(source=source, name=name)
        if "source" in result:
            _write_back_if_workspace(file, result["source"])
        emit(result, _get_format(ctx))
    except click.BadParameter:
        raise
    except ValueError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(7)
    except Exception as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# REMOTE COMMANDS (require API key)
# ═══════════════════════════════════════════════════════════════════════════════


def _client():
    from keel.client import KeelClient

    return KeelClient()


@universe.command()
@click.option("--mode", required=True, type=click.Choice(["manual", "category", "top_volume"]))
@click.option("--market", default="perp")
@click.option("--symbols", multiple=True)
@click.option("--categories", multiple=True)
@click.option("--top-n", type=int)
@click.option("--exclusions", multiple=True)
@click.option("--inclusions", multiple=True)
@click.pass_context
def resolve(
    ctx: click.Context,
    mode: str,
    market: str,
    symbols: tuple[str, ...],
    categories: tuple[str, ...],
    top_n: int | None,
    exclusions: tuple[str, ...],
    inclusions: tuple[str, ...],
) -> None:
    """Resolve universe criteria into a concrete symbol list (remote)."""
    from keel.errors import KeelError

    try:
        body: dict = {"mode": mode, "market": market}
        if symbols:
            body["symbols"] = list(symbols)
        if categories:
            body["categories"] = list(categories)
        if top_n is not None:
            body["top_n"] = top_n
        if exclusions:
            body["exclusions"] = list(exclusions)
        if inclusions:
            body["inclusions"] = list(inclusions)
        result = _client().post("/v1/universe/resolve", json=body)
        emit(result, _get_format(ctx))
    except KeelError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(e.exit_code)


@universe.command()
@click.pass_context
def categories(ctx: click.Context) -> None:
    """List available instrument categories (remote)."""
    from keel.errors import KeelError

    try:
        result = _client().get("/v1/universe/categories")
        emit(result, _get_format(ctx))
    except KeelError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(e.exit_code)


@universe.command()
@click.option("--market", default="perp", help="Market type filter")
@click.pass_context
def instruments(ctx: click.Context, market: str) -> None:
    """List available instruments (remote)."""
    from keel.errors import KeelError

    try:
        result = _client().get("/v1/universe/instruments", market=market)
        emit(result, _get_format(ctx))
    except KeelError as e:
        emit_error(e, _get_format(ctx))
        ctx.exit(e.exit_code)
