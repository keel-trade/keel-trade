"""Render an OutcomeTool as a Click command.

Each `OutcomeTool` declares a JSON Schema for its input. The adapter
turns that schema into Click options + arguments following these
conventions:

- `required` properties that look like IDs (suffix `_id`, or the first
  required string) become positional arguments
- Booleans → `--flag/--no-flag` with the schema default
- Enums → `click.Choice([...])`
- Arrays → `click.option(..., multiple=True)`
- Underscores in property names → hyphens in CLI flag names

`confirm_in_cli=True` adds a `--yes` flag. Human TTY callers get an
interactive prompt; detected agent-mode callers must pass `--yes`
explicitly after user confirmation.
"""

from __future__ import annotations

import json

import click

from keel.errors import KeelError
from keel.output import emit, emit_error

from ._base import OutcomeResult, OutcomeTool, ToolContext


def _flag_name(prop: str) -> str:
    return f"--{prop.replace('_', '-')}"


def _is_id_arg(prop: str, schema_prop: dict) -> bool:
    """Heuristic: required string that's an id or marked positional.

    Tools can opt a property in explicitly via `x-cli-positional: true`
    in the JSON schema; otherwise any required string ending in `_id`
    (or named `topic`) is treated as the canonical "thing you're
    operating on."
    """
    if schema_prop.get("x-cli-positional") is True:
        return True
    if schema_prop.get("type") != "string":
        return False
    return prop.endswith("_id") or prop in {"target_id", "topic"}


def _build_click_command(tool: OutcomeTool) -> click.Command:
    schema = tool.input_schema
    properties: dict = schema.get("properties", {})
    required: list[str] = list(schema.get("required", []))

    # Decide which property becomes a positional arg (at most one).
    # Prefer the first required string that looks like an id; fall back
    # to any property explicitly marked `x-cli-positional: true`.
    positional_arg: str | None = None
    for prop in required:
        if _is_id_arg(prop, properties.get(prop, {})):
            positional_arg = prop
            break
    if positional_arg is None:
        for prop, schema_prop in properties.items():
            if schema_prop.get("x-cli-positional") is True:
                positional_arg = prop
                break

    # Build the callback.
    def callback(**kwargs):
        # Normalise: hyphens → underscores already handled by Click's
        # `dest` rules. Translate `none`-valued args away so handlers see
        # a clean dict.
        args: dict = {k: v for k, v in kwargs.items() if v is not None}

        ctx_obj = click.get_current_context().obj or {}
        # Per-subcommand --format wins over top-level --format. Users
        # reach for `keel status --format json` first; the framework
        # should accept it there as well as `keel --format json status`.
        subcommand_fmt = args.pop("format", None)
        fmt = subcommand_fmt or ctx_obj.get("format", "human")
        dry_run = ctx_obj.get("dry_run", False) or args.pop("dry_run", False)

        yes = bool(kwargs.get("yes"))
        # Strip the CLI-only `yes` flag — it's already been honored by
        # the confirm prompt / agent-mode explicitness gate.
        args.pop("yes", None)

        if _requires_cli_confirmation(tool, args) and not yes:
            from keel.cli.agent_mode import is_agent_mode

            if is_agent_mode():
                _refuse_agent_confirmation(tool, args, fmt)

            import sys

            if sys.stdin.isatty():
                _confirm_destructive(tool, args)

        # Toolsets only filter the MCP surface, not the CLI — every
        # outcome is reachable on the command line.
        import os as _os
        _app_url = _os.environ.get("KEEL_APP_URL")
        tool_ctx = ToolContext(
            is_tty=_is_human_format(fmt),
            dry_run=dry_run,
            **({"app_url": _app_url} if _app_url else {}),
        )
        try:
            result = tool.handler(args, tool_ctx)
        except KeelError as e:
            emit_error(e, _output_fmt(fmt))
            raise click.exceptions.Exit(e.exit_code)
        except Exception as e:  # noqa: BLE001
            emit_error(
                KeelError(
                    f"Unexpected error in {tool.name}: {e}",
                    suggestion="Run `keel doctor` to diagnose.",
                ),
                _output_fmt(fmt),
            )
            raise click.exceptions.Exit(1)

        _render(result, fmt)

    # Decorate with options (reverse order: Click applies last decorator first).
    options: list = []
    for prop in sorted(properties.keys(), key=lambda p: (p != positional_arg, p)):
        if prop == positional_arg:
            continue
        opt = _prop_to_option(prop, properties[prop], required=prop in required)
        options.append(opt)

    cmd = click.pass_context(callback) if False else callback  # noqa: SIM108
    for opt in options:
        cmd = opt(cmd)

    if positional_arg:
        # Required iff the property is in the schema's required list.
        pos_required = positional_arg in required
        # Variadic positional for array types marked `x-cli-positional`:
        # `keel components describe-batch ROC EWMA ForecastScaler` is far
        # nicer than `--names ROC --names EWMA --names ForecastScaler`.
        positional_schema = properties.get(positional_arg, {})
        if positional_schema.get("type") == "array":
            cmd = click.argument(positional_arg, nargs=-1, required=pos_required)(cmd)
        else:
            cmd = click.argument(positional_arg, required=pos_required)(cmd)

    if tool.confirm_in_cli:
        cmd = click.option("--yes", is_flag=True, help="Skip confirmation prompt.")(cmd)

    # Accept --format at the subcommand level too — `keel status --format json`
    # is what users reach for first (the top-level form `keel --format json status`
    # also works and takes precedence only when the subcommand form is absent).
    cmd = click.option(
        "--format",
        "format",
        type=click.Choice(["json", "table", "tsv", "human"]),
        default=None,
        help="Override output format (overrides top-level --format)",
    )(cmd)

    # Last leaf name is the verb.
    leaf = tool.cli_path[-1].replace("_", "-")
    cmd = click.command(name=leaf, help=_one_line_help(tool.description))(cmd)
    return cmd


# Conventional short-flag aliases. Adding via the prop-name table beats
# requiring every tool to spell `x-cli-short` — `-m` for message is git's
# convention and an agent + user both reach for it without thinking.
_DEFAULT_SHORT_FLAGS = {
    "message": "-m",
    "ref": "-r",
    "limit": "-n",
}


def _flag_aliases(prop: str, schema: dict) -> list[str]:
    """Build the click.option flag args (long + optional short)."""
    flags = [_flag_name(prop)]
    short = schema.get("x-cli-short") or _DEFAULT_SHORT_FLAGS.get(prop)
    if short:
        flags.append(short)
    return flags


def _prop_to_option(prop: str, schema: dict, *, required: bool):
    flags = _flag_aliases(prop, schema)
    help_text = schema.get("description", "")
    schema_type = schema.get("type", "string")
    default = schema.get("default")

    if "enum" in schema:
        return click.option(
            *flags,
            prop,
            type=click.Choice(schema["enum"]),
            required=required,
            default=default,
            show_default=default is not None,
            help=help_text,
        )

    if schema_type == "boolean":
        # Click's `--flag/--no-flag` pattern lets the user explicitly
        # override the default in either direction. Boolean flags can't
        # also carry a short alias the way string options do, so we
        # skip the alias mechanism for them.
        no_flag = f"--no-{prop.replace('_', '-')}"
        return click.option(
            f"{flags[0]}/{no_flag}",
            default=default if default is not None else False,
            show_default=True,
            help=help_text,
        )

    if schema_type == "array":
        return click.option(
            *flags,
            prop,
            multiple=True,
            help=help_text,
        )

    if schema_type == "integer":
        return click.option(
            *flags,
            prop,
            type=int,
            required=required,
            default=default,
            show_default=default is not None,
            help=help_text,
        )

    if schema_type == "number":
        return click.option(
            *flags,
            prop,
            type=float,
            required=required,
            default=default,
            show_default=default is not None,
            help=help_text,
        )

    # Default: string-ish
    return click.option(
        *flags,
        prop,
        type=str,
        required=required,
        default=default,
        show_default=default is not None,
        help=help_text,
    )


def _one_line_help(description: str) -> str:
    """Trim a long MCP description to a single Click-help line."""
    first_sentence = description.split(". ")[0].strip().rstrip(".")
    return first_sentence


def _is_human_format(fmt: str) -> bool:
    return fmt in {"human", "table"}


def _output_fmt(fmt: str) -> str:
    """Normalise to the formats `emit()` understands."""
    return fmt if fmt in {"json", "tsv", "table", "human"} else "json"


def _requires_cli_confirmation(tool: OutcomeTool, args: dict) -> bool:
    """Return whether this concrete CLI call needs explicit confirmation."""
    if not tool.confirm_in_cli:
        return False
    # `keel live deploy` is destructive as a tool, but the default CLI
    # call is a preview-only staging step. Require confirmation only for
    # the actual deploy (`--no-preview` / preview=False).
    if tool.name == "keel_live_deploy" and args.get("preview", True) is True:
        return False
    return True


def _refuse_agent_confirmation(tool: OutcomeTool, args: dict, fmt: str) -> None:
    """Emit a structured refusal when an agent omits `--yes`."""
    command_path = click.get_current_context().command_path
    parts = command_path.split()
    if parts and parts[0] == "cli":
        parts[0] = "keel"
    command = " ".join(parts)

    if tool.name == "keel_share_create":
        expected = (
            "Explicit user confirmation that this target should be shared publicly. "
            "After confirmation, rerun the same command with `--yes`."
        )
    else:
        expected = (
            "Explicit user confirmation for this destructive CLI action. "
            "After confirmation, rerun the same command with `--yes`."
        )

    emit_error(
        KeelError(
            f"Agent-mode CLI requires `--yes` for {tool.name}.",
            error_code="cli_confirmation_required",
            exit_code=2,
            suggestion=expected,
            input={"command": f"{command} ... --yes", "args": args},
        ),
        _output_fmt(fmt),
    )
    raise click.exceptions.Exit(2)


def _confirm_destructive(tool: OutcomeTool, args: dict) -> None:
    """Prompt the user before a destructive action when they're at a
    TTY without `--yes`."""
    summary = ", ".join(f"{k}={v}" for k, v in args.items() if k != "yes") or "(no args)"
    click.confirm(
        f"This will run a DESTRUCTIVE action ({tool.name}) with: {summary}\nContinue?",
        abort=True,
    )


def _render(result: OutcomeResult, fmt: str) -> None:
    envelope = result.to_envelope()
    if _is_human_format(fmt):
        # All payload fields to stdout so the user can pipe/grep.
        # Spinners + status would go to stderr (we don't emit any
        # today). hero_url + share_url are the LAST lines on stdout
        # so they're easy to click — Vercel/Sentry pattern per spec §5.
        for k, v in envelope.items():
            if k in {"hero_url", "share_url", "resource_uri"}:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, indent=2)
            click.echo(f"{k}: {v}")
        if envelope.get("resource_uri"):
            click.echo(envelope["resource_uri"])
        if envelope.get("hero_url"):
            click.echo(envelope["hero_url"])
        if envelope.get("share_url"):
            click.echo(envelope["share_url"])
    else:
        emit(envelope, _output_fmt(fmt))


# Help text for each CLI group, surfaced at `keel --help`.
_GROUP_HELP: dict[tuple[str, ...], str] = {
    ("strategy",): "Create, search, fork, diff, and inspect strategies.",
    ("backtest",): "Run backtests and read their results.",
    ("live",): "Deploy, monitor, and control live trading deployments.",
    ("components",): "Search the component catalog and inspect schemas.",
    ("accounts",): "Read Hyperliquid trading accounts attached to your org.",
    ("share",): "Publish strategies and backtests at public usekeel.io/share URLs.",
    ("audit",): "Inspect agent / tool call history.",
    ("strategy", "memory"): "Read and append per-strategy notes (cross-conversation memory).",
}


# CLI verb aliases — keys are the canonical leaf name (matches the
# tool's `cli_path[-1]`), values are the additional verb names to also
# register. Used for UX-discoverability where the canonical tool name
# is misleading or non-obvious. Agents keep reaching for these
# alternatives by intuition; instead of fighting that, accept them.
_CLI_VERB_ALIASES: dict[tuple[str, ...], tuple[str, ...]] = {
    # `keel components compose-help <name>` is the canonical name (matches
    # the MCP tool `keel_components_compose_help`), but "describe" is what
    # every agent reaches for. The misnamed-tool finding is filed as P2 in
    # `projects/agent-v2/06-prod-readiness-followups.md`; the proper
    # rename ships in v0.5.0 as a breaking change. Until then, accept
    # both verb forms in the CLI so agents aren't blocked.
    ("components", "compose-help"): ("describe", "detail"),
}


def register_all(parent: click.Group, outcomes: dict[str, OutcomeTool]) -> None:
    """Attach every outcome tool as a CLI command under `parent`.

    Groups are derived from `tool.cli_path[:-1]`; the leaf becomes the
    command name. Any aliases declared in `_CLI_VERB_ALIASES` register
    the same Click command under additional names.
    """
    group_cache: dict[tuple[str, ...], click.Group] = {(): parent}

    for tool in outcomes.values():
        if tool.mcp_only:
            continue
        cmd = _build_click_command(tool)
        group_path = tool.cli_path[:-1]
        group = _ensure_group(parent, group_path, group_cache)
        group.add_command(cmd)
        # Register aliases (same callback, different verb name).
        aliases = _CLI_VERB_ALIASES.get(tool.cli_path, ())
        for alias_name in aliases:
            group.add_command(cmd, name=alias_name)


def _ensure_group(
    root: click.Group,
    path: tuple[str, ...],
    cache: dict[tuple[str, ...], click.Group],
) -> click.Group:
    if path in cache:
        return cache[path]
    parent = _ensure_group(root, path[:-1], cache)
    leaf = path[-1].replace("_", "-")
    grp = click.Group(name=leaf, help=_GROUP_HELP.get(path))
    parent.add_command(grp)
    cache[path] = grp
    return grp
