#!/usr/bin/env python3
"""Generate registry-derived MCP/CLI reference docs.

Run from the repo root:
    python packages/keel-trade/keel-sdk/scripts/generate_agent_reference_docs.py

Use --check in CI to fail when generated docs drift:
    python packages/keel-trade/keel-sdk/scripts/generate_agent_reference_docs.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import click


SCRIPT_DIR = Path(__file__).resolve().parent
SDK_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SDK_ROOT.parent.parent.parent

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from keel.cli.main import cli as KEEL_CLI  # noqa: E402
from keel.tools.outcomes import OUTCOMES, _bootstrap  # noqa: E402
from keel.tools.outcomes._toolsets import is_tool_loaded  # noqa: E402


PACKAGE_REFERENCE = REPO_ROOT / "packages" / "keel-trade" / "docs" / "tool-reference.md"
SITE_REFERENCE = (
    REPO_ROOT / "services" / "keel-site" / "content" / "docs" / "sdk" / "tool-reference.mdx"
)

DEFAULT_TOOLSETS = frozenset({"always", "read-only", "backtest", "share", "live-read"})


def _command_for_path(path: tuple[str, ...]) -> click.Command | None:
    current: click.Command = KEEL_CLI
    for part in path:
        if not isinstance(current, click.Group):
            return None
        current = current.commands.get(part.replace("_", "-"))
        if current is None:
            return None
    return current


def _all_leaf_commands(
    command: click.Command, path: tuple[str, ...] = ()
) -> dict[tuple[str, ...], click.Command]:
    if not isinstance(command, click.Group):
        return {path: command}

    leaves: dict[tuple[str, ...], click.Command] = {}
    for name, child in command.commands.items():
        leaves.update(_all_leaf_commands(child, path + (name,)))
    return leaves


def _param_label(prop: str, schema: dict) -> str:
    required_marker = "*" if schema.get("required") else ""
    typ = schema.get("type", "string")
    enum = schema.get("enum")
    if enum:
        typ = ", ".join(str(item) for item in enum)
    return f"`{prop}`{required_marker} ({typ})"


def _schema_args(tool_name: str) -> tuple[str, str]:
    schema = OUTCOMES[tool_name].input_schema
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    required_args: list[str] = []
    optional_args: list[str] = []
    for prop, prop_schema in properties.items():
        entry_schema = dict(prop_schema)
        entry_schema["required"] = prop in required
        label = _param_label(prop, entry_schema)
        if prop in required:
            required_args.append(label)
        else:
            optional_args.append(label)

    return ", ".join(required_args) or "-", ", ".join(optional_args) or "-"


def _one_line(text: str) -> str:
    compact = " ".join(text.split())
    first = compact.split(". Do NOT use", 1)[0]
    first = first.split(". ", 1)[0]
    return first.rstrip(".")


def _mdx_escape(text: str) -> str:
    """Escape prose that MDX would otherwise parse as JSX tags."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _annotation_summary(annotations: dict) -> str:
    tags: list[str] = []
    if annotations.get("readOnlyHint"):
        tags.append("read-only")
    else:
        tags.append("write")
    if annotations.get("destructiveHint"):
        tags.append("destructive")
    if annotations.get("idempotentHint"):
        tags.append("idempotent")
    if annotations.get("openWorldHint"):
        tags.append("open-world")
    return ", ".join(tags)


def _option_names(command: click.Command) -> str:
    names: list[str] = []
    for param in command.params:
        if isinstance(param, click.Option):
            visible = [opt for opt in param.opts + param.secondary_opts if opt != "--help"]
            names.extend(visible)
    return ", ".join(f"`{name}`" for name in names) or "-"


def _argument_names(command: click.Command) -> str:
    names = [
        f"`{param.name}`" if param.nargs != -1 else f"`{param.name}...`"
        for param in command.params
        if isinstance(param, click.Argument)
    ]
    return ", ".join(names) or "-"


def _cli_command_label(path: tuple[str, ...]) -> str:
    return "`keel " + " ".join(path) + "`"


def _table_cell(value: str) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(_table_cell(cell) for cell in cells) + " |"


def _outcome_table() -> str:
    lines = [
        _table_row(
            [
                "MCP tool",
                "CLI command",
                "Toolset",
                "Scope",
                "Annotations",
                "Required args",
                "Optional args",
            ]
        ),
        _table_row(["---", "---", "---", "---", "---", "---", "---"]),
    ]
    for name, tool in sorted(OUTCOMES.items()):
        required, optional = _schema_args(name)
        cli = _cli_command_label(tool.cli_path)
        if tool.mcp_only:
            cli += " (CLI-only implementation)"
        lines.append(
            _table_row(
                [
                    f"`{name}`",
                    cli,
                    f"`{tool.toolset}`",
                    f"`{tool.required_action or 'read'}`",
                    _annotation_summary(tool.annotations),
                    required,
                    optional,
                ]
            )
        )
    return "\n".join(lines)


def _tool_descriptions(*, mdx: bool) -> str:
    by_toolset: dict[str, list[str]] = {}
    for name, tool in sorted(OUTCOMES.items()):
        by_toolset.setdefault(tool.toolset, []).append(name)

    sections: list[str] = []
    for toolset in (
        "always",
        "read-only",
        "backtest",
        "share",
        "live-read",
        "live-write",
    ):
        names = by_toolset.get(toolset, [])
        if not names:
            continue
        sections.append(f"### `{toolset}`")
        for name in names:
            tool = OUTCOMES[name]
            description = _one_line(tool.description)
            if mdx:
                description = _mdx_escape(description)
            sections.append(f"- `{name}`: {description}.")
        sections.append("")
    return "\n".join(sections).strip()


def _cli_only_table() -> str:
    leaves = _all_leaf_commands(KEEL_CLI)
    outcome_paths = {tool.cli_path for tool in OUTCOMES.values()}
    lines = [
        _table_row(["CLI command", "Positional args", "Options"]),
        _table_row(["---", "---", "---"]),
    ]
    for path, command in sorted(leaves.items()):
        if path in outcome_paths:
            continue
        lines.append(
            _table_row(
                [
                    _cli_command_label(path),
                    _argument_names(command),
                    _option_names(command),
                ]
            )
        )
    return "\n".join(lines)


def _cli_outcome_table() -> str:
    lines = [
        _table_row(["CLI command", "MCP tool", "Positional args", "Options"]),
        _table_row(["---", "---", "---", "---"]),
    ]
    for name, tool in sorted(OUTCOMES.items()):
        command = _command_for_path(tool.cli_path)
        if command is None:
            args = "-"
            options = "-"
        else:
            args = _argument_names(command)
            options = _option_names(command)
        lines.append(
            _table_row(
                [
                    _cli_command_label(tool.cli_path),
                    f"`{name}`",
                    args,
                    options,
                ]
            )
        )
    return "\n".join(lines)


def render_reference(*, mdx: bool) -> str:
    _bootstrap()
    total_tools = len(OUTCOMES)
    default_tools = sum(
        1 for tool in OUTCOMES.values() if is_tool_loaded(tool.toolset, DEFAULT_TOOLSETS)
    )
    live_write_tools = sum(1 for tool in OUTCOMES.values() if tool.toolset == "live-write")

    parts: list[str] = []
    if mdx:
        parts.append(
            "---\n"
            "title: MCP Tool Reference\n"
            "description: Generated reference for Keel MCP outcome tools and their CLI equivalents.\n"
            "---"
        )

    parts.extend(
        [
            "# MCP Tool Reference",
            "> Generated from `keel.tools.outcomes.OUTCOMES` and the live Click command tree. Do not edit this file by hand; run `python packages/keel-trade/keel-sdk/scripts/generate_agent_reference_docs.py`.",
            f"Keel currently exposes **{total_tools} outcome tools**. The default local stdio MCP surface exposes **{default_tools} tools**, including read-only `keel_live_monitor`; the **{live_write_tools} live-write tools** are opt-in with `KEEL_TOOLSETS=always,read-only,backtest,share,live-read,live-write`.",
            "`KEEL_TOOLSETS=live` remains a deprecated compatibility alias for `live-read,live-write`; new configs should use the explicit split.",
            "MCP is the agent surface. The CLI is the reproducible terminal surface. Outcome tools share names, schemas, handlers, structured errors, and safety annotations across both surfaces.",
            "## Outcome Tools",
            _outcome_table(),
            "## Tool Descriptions",
            _tool_descriptions(mdx=mdx),
            "## CLI Equivalents",
            "Outcome commands generated from the registry:",
            _cli_outcome_table(),
            "## CLI-Only Commands",
            "These commands are intentionally not MCP tools. They handle local setup, credentials, project scaffolding, local context, local arming, and local universe files.",
            _cli_only_table(),
        ]
    )
    return "\n\n".join(parts).strip() + "\n"


def write_outputs() -> None:
    outputs = {
        PACKAGE_REFERENCE: render_reference(mdx=False),
        SITE_REFERENCE: render_reference(mdx=True),
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {path.relative_to(REPO_ROOT)}")


def check_outputs() -> int:
    expected = {
        PACKAGE_REFERENCE: render_reference(mdx=False),
        SITE_REFERENCE: render_reference(mdx=True),
    }
    drifted: list[Path] = []
    for path, content in expected.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            drifted.append(path)

    if not drifted:
        print("Generated agent reference docs are fresh.")
        return 0

    print("Generated agent reference docs are stale:", file=sys.stderr)
    for path in drifted:
        print(f"  - {path.relative_to(REPO_ROOT)}", file=sys.stderr)
    print(
        "Run: python packages/keel-trade/keel-sdk/scripts/generate_agent_reference_docs.py",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if generated docs are stale")
    args = parser.parse_args(argv)

    if args.check:
        return check_outputs()
    write_outputs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
