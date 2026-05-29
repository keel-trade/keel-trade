"""Keel CLI — main entry point.

The CLI and MCP server bind to one shared outcome-tool surface defined
in `keel.tools.outcomes` (see spec §4 + §12). A few non-tool CLI verbs
live alongside as escape hatches:

  - `keel auth ...` — credential management (CLI-only; not an MCP tool)
  - `keel mcp serve ...` — MCP stdio entrypoint (CLI-only)
  - `keel universe ...` — hand-edit local universe.yaml (CLI-only)
  - `keel skills ...` — skill management placeholder (Phase 2D rewrite)
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

import click

from keel.cli.agent_mode import default_format


try:
    _KEEL_VERSION = _pkg_version("keel-trade")
except PackageNotFoundError:
    _KEEL_VERSION = "0.0.0+unknown"


@click.group()
@click.version_option(version=_KEEL_VERSION, prog_name="keel")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "table", "tsv", "human"]),
    default=None,
    help="Output format (default: auto-detect from context)",
)
@click.option("--dry-run", is_flag=True, help="Preview without side effects")
@click.option("--verbose", is_flag=True, help="Verbose output")
@click.pass_context
def cli(ctx: click.Context, fmt: str | None, dry_run: bool, verbose: bool) -> None:
    """Keel — AI-native strategy development CLI."""
    ctx.ensure_object(dict)
    ctx.obj["format"] = fmt or default_format()
    ctx.obj["dry_run"] = dry_run
    ctx.obj["verbose"] = verbose


def _get_format(ctx: click.Context) -> str:
    """Get output format from Click context."""
    return ctx.obj.get("format", "json")


# ── Outcome tool surface (CLI + MCP share these) ────────────────────────

from keel.tools.outcomes import _bootstrap as _outcomes_bootstrap, OUTCOMES  # noqa: E402
from keel.tools.outcomes._cli_adapter import register_all as _outcomes_cli_register  # noqa: E402

_outcomes_bootstrap()
_outcomes_cli_register(cli, OUTCOMES)


# ── CLI-only escape-hatch verbs (not exposed as MCP tools) ──────────────

from keel.cli.commands.auth import auth  # noqa: E402
from keel.cli.commands.mcp_cmd import mcp  # noqa: E402
from keel.cli.commands.universe import universe  # noqa: E402
from keel.cli.commands.skills import skills  # noqa: E402
from keel.cli.commands.arm import arm  # noqa: E402
from keel.cli.commands.context import context  # noqa: E402
from keel.cli.commands.project import project  # noqa: E402

cli.add_command(auth)
cli.add_command(mcp)
cli.add_command(universe)
cli.add_command(skills)
cli.add_command(arm)
cli.add_command(context)
cli.add_command(project)
