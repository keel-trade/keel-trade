"""Generate the public skills manifest — `.well-known/skills/index.json`.

Renders ``services/keel-site/public/.well-known/skills/index.json``
(served at ``https://usekeel.io/.well-known/skills/index.json``) from
the live bundled-skill registry (``keel.skills.list_skills()``) — the
same source the CLI (``keel skills list``) and the MCP prompts surface
render from. Follows the Stripe-style shape (top-level ``skills`` array
of ``{name, description, ...}``) with per-skill install/usage pointers
instead of hosted file paths, since Keel skills ship inside the
``keel-trade`` package rather than as fetchable docs.

Drift-gated by ``tests/test_skills_manifest.py`` (monorepo only): any
change to a skill's frontmatter without regeneration fails CI.

Regenerate:

    python packages/keel-trade/keel-sdk/scripts/build_skills_manifest.py

Never hand-edit the generated JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parents[2]
MANIFEST_PATH = (
    REPO_ROOT / "services" / "keel-site" / "public" / ".well-known" / "skills" / "index.json"
)

INSTALL_BLOCK = {
    "package": "keel-trade",
    "pypi": "https://pypi.org/project/keel-trade/",
    "command": "pipx install keel-trade",
    "mcp_server": "keel mcp serve",
    "mcp_registry": "io.github.keel-trade/keel-trade",
    "claude_desktop_bundle": (
        "https://github.com/keel-trade/keel-trade/releases/latest/download/keel-trade-latest.mcpb"
    ),
    "docs": "https://usekeel.io/keel-mcp",
}


def _one_line(text: str) -> str:
    """Collapse multi-line frontmatter strings to a single line."""
    return " ".join(text.split())


def build_manifest() -> dict:
    """Build the manifest dict from the live skill registry."""
    sys.path.insert(0, str(SDK_ROOT))
    from keel.skills import BUNDLED_SKILLS, list_skills

    skills_map = list_skills()
    skills = []
    for name in BUNDLED_SKILLS:
        skill = skills_map[name]
        skills.append(
            {
                "name": skill.name,
                "description": _one_line(skill.description),
                "trigger": _one_line(skill.trigger),
                "install": "pipx install keel-trade",
                "usage": f"keel skills show {skill.name}",
                "mcp_prompt": skill.name,
            }
        )

    return {
        "product": "Keel",
        "website": "https://usekeel.io",
        "description": (
            "Agent skills bundled with the keel-trade package (CLI + stdio "
            "MCP server) for building, backtesting, and deploying systematic "
            "trading strategies on Hyperliquid. Each skill is an Anthropic "
            "Agent Skill: markdown workflow + composed platform knowledge, "
            "exposed via `keel skills` on the CLI and as MCP prompts "
            "(prompts/list / prompts/get) in MCP hosts."
        ),
        "install": INSTALL_BLOCK,
        "skills": skills,
    }


def render_manifest() -> str:
    return json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(render_manifest(), encoding="utf-8")
    print(f"wrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
