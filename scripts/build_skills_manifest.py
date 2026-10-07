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
change to a skill's frontmatter without regeneration fails CI. Since
agent-surface 2.8 the file is ALSO a whole-file consumer of
``check_surface_routing.py`` (``--write`` renders it): the ``hosted``
block and the hosted-first install pointers come from
``shared/agent-surface.json`` + ``LISTED_EXCLUDED_SKILLS`` (Q-1467).

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
AGENT_SURFACE_PATH = REPO_ROOT / "shared" / "agent-surface.json"

INSTALL_BLOCK = {
    "package": "keel-trade",
    "pypi": "https://pypi.org/project/keel-trade/",
    "command": "pipx install keel-trade",
    "mcp_server": "keel mcp serve",
    "mcp_registry": "io.github.keel-trade/keel-trade",
    "claude_desktop_bundle": (
        "https://github.com/keel-trade/keel-trade/releases/latest/download/keel-trade-latest.mcpb"
    ),
}


def _one_line(text: str) -> str:
    """Collapse multi-line frontmatter strings to a single line."""
    return " ".join(text.split())


def build_manifest() -> dict:
    """Build the manifest dict from the live skill registry."""
    sys.path.insert(0, str(SDK_ROOT))
    from keel.mcp.server import LISTED_EXCLUDED_SKILLS
    from keel.skills import BUNDLED_SKILLS, list_skills

    surface = json.loads(AGENT_SURFACE_PATH.read_text(encoding="utf-8"))
    endpoint = surface["endpoint"]["hosted"]
    served = [name for name in BUNDLED_SKILLS if name not in LISTED_EXCLUDED_SKILLS]
    excluded = [name for name in BUNDLED_SKILLS if name in LISTED_EXCLUDED_SKILLS]
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
            "Agent skills for building, backtesting and reading systematic "
            "strategies on Hyperliquid. Each skill is an Anthropic Agent Skill: a "
            "markdown workflow plus composed platform knowledge. The hosted "
            "endpoint serves them through the keel_help tool; the keel-trade "
            "package bundles the same set for the CLI (`keel skills`) and the "
            "local stdio MCP server (prompts/list / prompts/get)."
        ),
        "hosted": {
            "endpoint": endpoint,
            "setup": surface["setup_page_url"],
            "tool_path": (
                'keel_help(topic="skills") lists them; keel_help(topic="skill:<name>") returns one'
            ),
            "served": served,
            "excluded": excluded,
        },
        "install": {
            "hosted_endpoint": endpoint,
            "hosted_setup": surface["setup_page_url"],
            **INSTALL_BLOCK,
            "docs": surface["agents_page_url"],
        },
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
