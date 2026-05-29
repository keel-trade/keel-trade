"""Tests for Phase 2D — Anthropic Agent Skills bundled with keel-trade.

The 8 bundled skills live at `packages/keel-trade/keel-sdk/keel/skills/*.md`.
Each composes from `the upstream reference system docs` (vendored
to `keel/data/knowledge/`) plus its own workflow body. See spec §11.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

SKILLS_DIR = Path(__file__).parent.parent / "keel" / "skills"

EXPECTED_SKILLS = [
    "strategy-creation",
    "strategy-fork-and-iterate",
    "backtest-and-analyze",
    "overfit-check",
    "deploy-and-monitor",
    "portfolio-review",
    "component-discovery",
    "recover-from-error",
]

REQUIRED_SECTIONS = [
    "# Workflow",
    "# Common mistakes",
    "# Expected output shape",
    "# When NOT to use this skill",
    "# Test prompts",
]


# ── Skill .md files exist and are well-formed ─────────────────────────────


class TestSkillFiles:
    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_file_exists(self, skill_name):
        skill_file = SKILLS_DIR / f"{skill_name}.md"
        assert skill_file.exists(), f"Missing {skill_file}"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_has_valid_frontmatter(self, skill_name):
        content = (SKILLS_DIR / f"{skill_name}.md").read_text()
        assert content.startswith("---"), f"{skill_name} missing frontmatter"
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"{skill_name} malformed frontmatter"
        fm = yaml.safe_load(parts[1])
        for field in ("name", "description", "trigger", "knowledge", "tools"):
            assert field in fm, f"{skill_name} frontmatter missing '{field}'"
        assert fm["name"] == skill_name, f"{skill_name} frontmatter name mismatch"
        assert isinstance(fm["knowledge"], list) and len(fm["knowledge"]) >= 1
        assert isinstance(fm["tools"], list) and len(fm["tools"]) >= 1

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_frontmatter_tools_exist_in_outcome_registry(self, skill_name):
        """Skill tool lists are MCP-facing, so every `keel_*` tool named
        there must exist in the current outcome registry."""
        from keel.tools.outcomes import OUTCOMES, _bootstrap

        _bootstrap()
        content = (SKILLS_DIR / f"{skill_name}.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        missing = [tool for tool in fm["tools"] if tool not in OUTCOMES]
        assert missing == []

    def test_skill_bodies_do_not_reference_known_stale_mcp_shapes(self):
        """Catch drift where SDK skills accidentally document chat-only
        resources or old argument names instead of the current MCP surface."""
        stale_patterns = {
            "component_name=<name>": "keel_components_compose_help expects name",
            "keel://strategy/list": "use keel_strategy_search instead",
            "keel://deployment/<id>/full": "use keel_live_monitor views instead",
            "summary=<": "keel_strategy_memory_write expects note",
            "share=<id>": "keel_strategy_fork expects source",
            "from_version=": "keel_strategy_diff expects ref_a/ref_b",
            "to_version=": "keel_strategy_diff expects ref_a/ref_b",
        }
        all_text = "\n".join(p.read_text() for p in sorted(SKILLS_DIR.glob("*.md")))
        offenders = {
            pattern: reason
            for pattern, reason in stale_patterns.items()
            if pattern in all_text
        }
        assert offenders == {}

        deploy_text = (SKILLS_DIR / "deploy-and-monitor.md").read_text()
        assert "dry_run" not in deploy_text

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_has_required_sections(self, skill_name):
        """Per spec §11.3: Workflow / Common mistakes / Expected output shape
        / When NOT to use / Test prompts — all five, in order."""
        content = (SKILLS_DIR / f"{skill_name}.md").read_text()
        last_idx = -1
        for section in REQUIRED_SECTIONS:
            idx = content.find(section)
            assert idx >= 0, f"{skill_name} missing section '{section}'"
            assert idx > last_idx, (
                f"{skill_name} sections out of order — '{section}' "
                f"appears before earlier required section"
            )
            last_idx = idx

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_description_and_trigger_are_tight(self, skill_name):
        """Spec §11.1: name + description + trigger should be ~500 tokens
        total across all 8 skills (~60/skill). Per-skill budget ~250 chars
        each for description and trigger after collapsing whitespace."""
        content = (SKILLS_DIR / f"{skill_name}.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        desc_len = len(" ".join(fm["description"].split()))
        trig_len = len(" ".join(fm["trigger"].split()))
        assert desc_len <= 400, (
            f"{skill_name} description is {desc_len} chars (target <=400)"
        )
        assert trig_len <= 500, (
            f"{skill_name} trigger is {trig_len} chars (target <=500)"
        )


# ── Loader and knowledge resolution ──────────────────────────────────────


class TestLoader:
    def test_list_skills_returns_all_eight(self):
        from keel.skills import BUNDLED_SKILLS, list_skills

        assert len(BUNDLED_SKILLS) == 8
        assert set(BUNDLED_SKILLS) == set(EXPECTED_SKILLS)
        skills_map = list_skills()
        assert set(skills_map.keys()) == set(EXPECTED_SKILLS)

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_load_skill_parses(self, skill_name):
        from keel.skills import load_skill

        sk = load_skill(skill_name)
        assert sk.name == skill_name
        assert sk.description
        assert sk.trigger
        assert sk.knowledge
        assert sk.tools
        assert sk.body
        assert "# Workflow" in sk.body

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_knowledge_sections_resolve(self, skill_name):
        """Every listed knowledge section must resolve to non-empty
        content via the bundled `keel.data.knowledge` loader."""
        from keel.data.knowledge import load_section
        from keel.skills import load_skill

        sk = load_skill(skill_name)
        for section in sk.knowledge:
            text = load_section(section)
            assert text, f"{skill_name}: section '{section}' loaded empty"
            assert len(text) > 100, (
                f"{skill_name}: section '{section}' suspiciously short ({len(text)} chars)"
            )

    def test_compose_strategy_creation_includes_all_knowledge(self):
        from keel.skills import compose_skill, load_skill

        sk = load_skill("strategy-creation")
        composed = compose_skill("strategy-creation")
        # Each listed section's header marker should appear once.
        for section in sk.knowledge:
            assert f"## Knowledge: {section}" in composed, (
                f"composed strategy-creation missing section '{section}'"
            )
        # Skill body markers also appear.
        for required in REQUIRED_SECTIONS:
            assert required in composed

    def test_load_unknown_skill_raises(self):
        from keel.skills import load_skill

        with pytest.raises(FileNotFoundError):
            load_skill("does-not-exist")


# ── CLI surface ──────────────────────────────────────────────────────────


class TestCLI:
    def test_keel_skills_list_shows_eight(self):
        from keel.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--format", "json", "skills", "list"])
        assert result.exit_code == 0, result.output
        import json as _json

        rows = _json.loads(result.output)
        assert len(rows) == 8
        assert {r["name"] for r in rows} == set(EXPECTED_SKILLS)
        for row in rows:
            assert row["description"]
            assert row["trigger"]

    def test_keel_skills_show_strategy_creation_contains_knowledge(self):
        from keel.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--format", "json", "skills", "show", "strategy-creation"]
        )
        assert result.exit_code == 0, result.output
        import json as _json

        body = _json.loads(result.output)
        assert body["name"] == "strategy-creation"
        content = body["content"]
        # Frontmatter
        assert content.startswith("---")
        assert "# Tool Surface Note" in content
        assert "use the `keel_*` tools" in content
        # All listed knowledge sections concatenated in
        for section in (
            "reasoning_principles",
            "composition_mechanics",
            "dsl_syntax",
            "mistakes",
            "tool_usage",
            "universe_selection",
            "pipeline_system",
        ):
            assert f"## Knowledge: {section}" in content, (
                f"composed strategy-creation missing '{section}'"
            )
        # Body sections
        for required in REQUIRED_SECTIONS:
            assert required in content

    def test_keel_skills_show_unknown_errors(self):
        from keel.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["skills", "show", "no-such-skill"])
        assert result.exit_code != 0
