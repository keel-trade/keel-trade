"""`keel project init` — write multi-IDE project context templates.

Per spec §10.
"""

from __future__ import annotations

from pathlib import Path

import click

from keel.cli.main import _get_format
from keel.output import emit
from keel.project import claude_hooks_config, detect_ide, init_project


@click.group()
def project() -> None:
    """Project-level commands (init templates + agent hooks)."""


@project.command("init")
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files. Default skips files that already exist.",
)
@click.pass_context
def project_init(ctx: click.Context, force: bool) -> None:
    """Write CLAUDE.md, AGENTS.md, .cursorrules, .windsurf/rules.md and
    .keel/workspace.yaml to the current directory.

    Detects the active IDE for the success-message banner; all files
    are written regardless of which IDE the user is in. Files that
    already exist are skipped unless --force is passed.
    """
    written = init_project(force=force)
    payload = {
        "detected_ide": detect_ide(),
        "force": force,
        "files": [
            {
                "path": str(item.path),
                "existed_before": item.existed_before,
                "skipped": item.skipped,
            }
            for item in written
        ],
    }
    emit(payload, _get_format(ctx))


@project.command("hooks")
@click.pass_context
def project_hooks(ctx: click.Context) -> None:
    """Print the Claude Code hook config block.

    Pipe to a settings file or paste into `.claude/settings.json`'s
    `hooks` array.
    """
    click.echo(claude_hooks_config())
