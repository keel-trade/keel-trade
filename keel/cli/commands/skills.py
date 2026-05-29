"""`keel skills` — list and inspect the bundled Anthropic Agent Skills.

The 8 bundled skills (spec §11.2) compose at runtime from
`the upstream reference system docs` + each skill's own
workflow body. See `keel/skills/__init__.py` for the loader and
`keel/skills/*.md` for the source files.

Commands:
    keel skills list             — name + description + trigger for all 8
    keel skills show <name>      — fully composed skill content
"""

from __future__ import annotations

import click

from keel.cli.main import _get_format
from keel.output import emit, emit_error
from keel.skills import BUNDLED_SKILLS, compose_skill, list_skills


@click.group()
def skills() -> None:
    """Behavioral skills — Anthropic Agent Skills bundled with keel-trade."""


_FORMAT_CHOICES = click.Choice(["json", "table", "tsv", "human"])


def _resolve_format(ctx: click.Context, override: str | None) -> str:
    """Per-subcommand `--format` overrides the group-level flag."""
    return override or _get_format(ctx)


@skills.command("list")
@click.option("--format", "fmt", type=_FORMAT_CHOICES, default=None,
              help="Output format (default: inherit from `keel --format`)")
@click.pass_context
def list_skills_cmd(ctx: click.Context, fmt: str | None) -> None:
    """List bundled skills with name, description, and trigger."""
    skills_map = list_skills()
    rows = []
    for name in BUNDLED_SKILLS:
        sk = skills_map[name]
        rows.append(
            {
                "name": sk.name,
                "description": _one_line(sk.description),
                "trigger": _one_line(sk.trigger),
            }
        )
    emit(rows, _resolve_format(ctx, fmt), columns=["name", "description", "trigger"])


@skills.command("show")
@click.argument("name")
@click.option("--format", "fmt", type=_FORMAT_CHOICES, default=None,
              help="Output format (default: inherit from `keel --format`)")
@click.pass_context
def show_skill_cmd(ctx: click.Context, name: str, fmt: str | None) -> None:
    """Print the fully-composed body of a skill: frontmatter + loaded
    knowledge sections + skill workflow body."""
    resolved = _resolve_format(ctx, fmt)
    try:
        body = compose_skill(name)
    except FileNotFoundError as exc:
        emit_error(str(exc), resolved)
        ctx.exit(1)
        return

    if resolved == "json":
        emit({"name": name, "content": body}, resolved)
    else:
        # For human / table / tsv, print the raw markdown — the body
        # is itself a structured document.
        click.echo(body)


def _one_line(text: str) -> str:
    """Collapse multi-line frontmatter strings to a single line for the
    list view. Preserves the full content; just elides newlines."""
    return " ".join(text.split())
