"""3-layer user context — global / project / strategy.

Spec §9 (lines 1104-1230). The agent reads context on session start
without asking; survives across sessions, devices, and clients.

Layers:
  - **global**: `~/.keel/context.md` — per-user, all projects
  - **project**: `<cwd>/keel.md` (preferred) or the `## Keel` block in
    `<cwd>/CLAUDE.md` — per-repo
  - **strategy**: server-side, fetched lazily via the
    `keel_strategy_memory_read` outcome tool

The MCP server exposes layers 1 + 2 as resources:
  - `keel://context/user`
  - `keel://context/project`
Strategy memory has its own resource `keel://context/strategy/{id}`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_KEEL_BLOCK_PATTERN = re.compile(
    r"^##\s+Keel\s*$(?P<body>.*?)(?=^##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass
class ContextEntry:
    layer: str  # "user" | "project" | "strategy"
    source: Path | None
    body: str
    exists: bool


def home_context_path() -> Path:
    return Path.home() / ".keel" / "context.md"


def project_context_path(cwd: Path | None = None) -> Path:
    """Resolve the project-layer context path.

    Preference order:
      1. `<cwd>/keel.md` (explicit, single-purpose)
      2. `## Keel` markdown block inside `<cwd>/CLAUDE.md` (per spec,
         lets users keep one project file)
      3. `<cwd>/.keel/context.md` (alternate explicit location)
    The caller decides which to read; `read_project_context` searches in
    the same order.
    """
    cwd = cwd or Path.cwd()
    return cwd / "keel.md"


def read_user_context() -> ContextEntry:
    path = home_context_path()
    if not path.exists():
        return ContextEntry(layer="user", source=path, body="", exists=False)
    return ContextEntry(
        layer="user", source=path, body=path.read_text(encoding="utf-8"), exists=True
    )


def read_project_context(cwd: Path | None = None) -> ContextEntry:
    cwd = cwd or Path.cwd()

    # 1. keel.md
    keel_md = cwd / "keel.md"
    if keel_md.exists():
        return ContextEntry(
            layer="project", source=keel_md, body=keel_md.read_text(encoding="utf-8"), exists=True
        )

    # 2. ## Keel block inside CLAUDE.md
    claude_md = cwd / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8")
        match = _KEEL_BLOCK_PATTERN.search(text)
        if match:
            return ContextEntry(
                layer="project",
                source=claude_md,
                body=match.group("body").strip(),
                exists=True,
            )

    # 3. .keel/context.md alternate
    alt = cwd / ".keel" / "context.md"
    if alt.exists():
        return ContextEntry(
            layer="project", source=alt, body=alt.read_text(encoding="utf-8"), exists=True
        )

    return ContextEntry(layer="project", source=None, body="", exists=False)


def write_user_context(body: str) -> Path:
    """Replace the global context file. Creates parent dirs."""
    path = home_context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def append_user_context(note: str) -> Path:
    """Append a timestamped note to the global context file."""
    from datetime import datetime, timezone

    path = home_context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_line = f"\n- ({timestamp}) {note.strip()}\n"

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        # If there's a "## Notes" section, append within it; otherwise add one.
        if "\n## Notes" in existing:
            updated = re.sub(
                r"(##\s+Notes\s*$\n)",
                r"\g<1>" + new_line,
                existing,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            updated = existing.rstrip() + "\n\n## Notes\n" + new_line
        path.write_text(updated, encoding="utf-8")
    else:
        path.write_text(f"# Keel global context\n\n## Notes\n{new_line}", encoding="utf-8")
    return path


_DEFAULT_INIT_TEMPLATE = """\
# Keel context for {user}

## Identity
<Quant background, trading capital, primary venue, risk tolerance.>

## Default universe
<Default symbols / filters when running new strategies.>

## Favorite signals
<Components or signal patterns you reach for first.>

## Risk preferences
<Hard limits the agent should enforce (leverage, drawdown, etc.).>

## Workflow preferences
<Default date ranges, output formats, confirmation behavior.>

## Custom prompt fragments
<Anything you want the agent to ALWAYS do that Keel doesn't natively support.>
"""


def init_user_context(user: str = "you", overwrite: bool = False) -> Path:
    """Write the default 6-section template to `~/.keel/context.md`."""
    path = home_context_path()
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"{path} already exists. Pass --force to overwrite, or run `keel context edit`."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_INIT_TEMPLATE.format(user=user), encoding="utf-8")
    return path


def read_layer(layer: str, cwd: Path | None = None) -> ContextEntry:
    """Generic accessor used by the MCP resource handlers."""
    if layer == "user":
        return read_user_context()
    if layer == "project":
        return read_project_context(cwd)
    raise ValueError(f"Unknown context layer: {layer!r}. Use 'user' or 'project'.")
