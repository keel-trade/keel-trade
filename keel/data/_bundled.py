"""The one reader of a bundled file chosen by NAME (Q-3352).

Knowledge sections, DSL reference topics and agent skills are addressed by a
name that can arrive from a caller: an MCP resource URI
(`keel://knowledge/{section}`, `keel://dsl/reference/{topic}`), a
`keel_help` topic, a prompt name. Those loaders used to join the name onto
the package directory and open the result, consulting the directory listing
only after the open failed. That made the name a filesystem path: on the
hosted server `../`, an absolute name or an over-long name reached `open()`,
and the only barrier was fastmcp's default resource screening.

Here the name is never part of a path. It is a KEY into the directory's own
listing, and the file handle comes from `iterdir()`. A name that is not a key
is refused before anything is touched on disk, with a plain sentence that
lists the public names and never echoes the input or a server path. As
defence in depth, a listed file must still resolve inside its directory (a
symlink in the package cannot point the read elsewhere).
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from keel.errors import NotFoundError


__all__ = ["MARKDOWN_FILES", "SKILL_DIRS", "bundled_names", "read_bundled"]

#: ``<dir>/<name>.md`` — knowledge sections, DSL reference topics.
MARKDOWN_FILES = "markdown_files"
#: ``<dir>/<name>/SKILL.md`` — the Agent Skills layout.
SKILL_DIRS = "skill_dirs"


def _base(package: str, subdir: str) -> Traversable:
    base = resources.files(package)
    return base.joinpath(subdir) if subdir else base


def _entries(base: Traversable, layout: str) -> dict[str, Traversable]:
    """Name → file, read off the directory. The ONLY source of names."""
    if layout == MARKDOWN_FILES:
        return {
            entry.name[: -len(".md")]: entry
            for entry in base.iterdir()
            if entry.name.endswith(".md") and entry.is_file()
        }
    if layout == SKILL_DIRS:
        out: dict[str, Traversable] = {}
        for entry in base.iterdir():
            if entry.is_dir() and entry.joinpath("SKILL.md").is_file():
                out[entry.name] = entry.joinpath("SKILL.md")
        return out
    raise ValueError(f"unknown bundled layout {layout!r}")


def bundled_names(package: str, subdir: str, layout: str) -> tuple[str, ...]:
    """Every name ``read_bundled`` accepts for this directory, sorted."""
    return tuple(sorted(_entries(_base(package, subdir), layout)))


def _inside(base: Traversable, entry: Traversable) -> bool:
    """A filesystem entry resolves inside its directory. Regular packages
    installed from a wheel (and the unpacked .mcpb tree) are always on the
    filesystem; a non-filesystem Traversable (a zip import) has no path that
    could escape, and its entry came from ``iterdir()`` of ``base``."""
    if isinstance(base, Path) and isinstance(entry, Path):
        return entry.resolve().is_relative_to(base.resolve())
    return True


def read_bundled(package: str, subdir: str, name: object, *, layout: str, kind: str) -> str:
    """The text of the bundled file called ``name``, or ``NotFoundError``.

    ``kind`` names what was asked for in the refusal ("knowledge section").
    The refusal lists the available names — they are the public index — and
    never repeats the input or a path.
    """
    base = _base(package, subdir)
    entries = _entries(base, layout)
    entry = entries.get(name) if isinstance(name, str) else None
    if entry is None or not _inside(base, entry):
        raise NotFoundError(
            f"Unknown {kind}. Available: {', '.join(sorted(entries))}.",
            suggestion=f"Ask for one of the listed {kind}s by its exact name.",
        )
    return entry.read_text(encoding="utf-8")
