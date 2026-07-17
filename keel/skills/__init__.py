"""Bundled Anthropic Agent Skills for the Keel MCP / CLI surface.

Each `keel/skills/<name>.md` is a markdown file with YAML frontmatter
(`name`, `description`, `trigger`, `knowledge`, `tools`) and a body
with five required sections (Workflow / Common mistakes / Expected
output shape / When NOT to use this skill / Test prompts).

At session start only `name + description + trigger` should be exposed
(~60 tokens per skill). Full skill content — frontmatter + composed
knowledge sections from `the upstream reference system docs` +
the skill body — loads on demand via `compose_skill()`.

The knowledge sections are loaded from `keel.data.knowledge` (the
bundled copy of `the upstream reference system docs` regenerated
by `packages/keel-trade/keel-sdk/scripts/build_data.py`).

See spec §11 in `projects/agent-v2/03-ideal-experience-spec.md` for the
design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import yaml

from keel.data.knowledge import load_section


@dataclass(frozen=True)
class Skill:
    """A parsed bundled skill.

    Attributes:
        name: Canonical skill name (matches the filename stem).
        description: One-paragraph "what this skill does".
        trigger: When to use / when NOT to use (free-text).
        knowledge: List of knowledge sections to load on activation
            (each matches a file stem under
            `the upstream reference system docs`).
        tools: List of MCP tool names this skill orchestrates.
        body: The markdown body (Workflow / Common mistakes / etc.).
    """

    name: str
    description: str
    trigger: str
    knowledge: tuple[str, ...]
    tools: tuple[str, ...]
    body: str


# Public list of bundled skill names — also used by `keel skills list`
# and the MCP prompts registration. Order is the §11.2 canonical order.
BUNDLED_SKILLS = (
    "strategy-creation",
    "strategy-fork-and-iterate",
    "backtest-and-analyze",
    "overfit-check",
    "deploy-and-monitor",
    "portfolio-review",
    "component-discovery",
    "recover-from-error",
)


def _skills_dir():
    """Return a Traversable pointing at the bundled skills directory."""
    return resources.files("keel.skills")


def _read_skill_file(name: str) -> str:
    """Read a bundled skill file by name (without .md)."""
    ref = _skills_dir().joinpath(f"{name}.md")
    try:
        return ref.read_text()
    except FileNotFoundError as exc:
        available = list_skills()
        raise FileNotFoundError(
            f"Unknown skill '{name}'. Available: {sorted(available.keys())}"
        ) from exc


def _parse(raw: str, name: str) -> Skill:
    """Split frontmatter from body and build a Skill."""
    if not raw.startswith("---"):
        raise ValueError(f"Skill '{name}' missing YAML frontmatter (no leading ---)")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Skill '{name}' has malformed frontmatter (need two --- fences)")
    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return Skill(
        name=fm.get("name", name),
        description=(fm.get("description") or "").strip(),
        trigger=(fm.get("trigger") or "").strip(),
        knowledge=tuple(fm.get("knowledge") or ()),
        tools=tuple(fm.get("tools") or ()),
        body=body,
    )


@lru_cache(maxsize=None)
def load_skill(name: str) -> Skill:
    """Parse and return a single skill by name."""
    return _parse(_read_skill_file(name), name)


@lru_cache(maxsize=1)
def list_skills() -> dict[str, Skill]:
    """Return all bundled skills, name → Skill. Cached."""
    out: dict[str, Skill] = {}
    for name in BUNDLED_SKILLS:
        out[name] = load_skill(name)
    return out


def compose_skill(name: str) -> str:
    """Return the fully-composed skill content for activation.

    Format:
        <frontmatter block>
        <concatenated knowledge sections>
        <skill body>

    Knowledge sections are loaded from the bundled
    `keel.data.knowledge` copy of `the upstream reference system docs`.
    Sections missing on disk are skipped silently.
    """
    skill = load_skill(name)
    fm_lines = [
        "---",
        f"name: {skill.name}",
        f"description: |\n  {_indent(skill.description, '  ')}",
        f"trigger: |\n  {_indent(skill.trigger, '  ')}",
        "knowledge:",
    ]
    fm_lines.extend(f"  - {k}" for k in skill.knowledge)
    fm_lines.append("tools:")
    fm_lines.extend(f"  - {t}" for t in skill.tools)
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)

    surface_note = (
        "\n\n# Tool Surface Note\n\n"
        "The loaded knowledge below is shared with Keel's in-app chat service "
        "and may mention chat tool names such as `strategy_components_search`, "
        "`strategy_component_detail_batch`, `update_strategy`, `pipeline_stage`, "
        "or `run_backtest`. In this SDK/MCP skill, use the `keel_*` tools listed "
        "in this skill's frontmatter and workflow body. Treat the active MCP "
        "`tools/list` schemas as authoritative for argument names.\n"
    )

    knowledge_parts: list[str] = [surface_note]
    if skill.knowledge:
        knowledge_parts.append("\n\n# Loaded knowledge\n")
        for section in skill.knowledge:
            try:
                text = load_section(section)
            except FileNotFoundError:
                continue
            knowledge_parts.append(f"\n## Knowledge: {section}\n\n{text}")

    return frontmatter + "".join(knowledge_parts) + "\n\n" + skill.body


def _indent(text: str, prefix: str) -> str:
    """Indent every line after the first by `prefix` (YAML block scalar)."""
    lines = text.splitlines() or [""]
    return ("\n" + prefix).join(lines)


__all__ = [
    "BUNDLED_SKILLS",
    "Skill",
    "compose_skill",
    "list_skills",
    "load_skill",
]
