"""Tests for the bundled Anthropic Agent Skills — method plus index.

The 8 bundled skills live at `packages/keel-trade/keel-sdk/keel/skills/<name>/SKILL.md`
(the portable Agent Skills layout — agent-distribution spec 02 R1).

Since the guidance-architecture build (spec §3 L3) a skill is METHOD
ONLY — steps, decision points, output shape, when-not-to-use — plus a
plain REFERENCE INDEX listing the Reference topics it cites. Nothing is
appended at composition time, which is what keeps activation inside the
5,000-token budget that appending `dsl_syntax` + `composition_mechanics`
alone would blow.

Since mcp-conversion 05 §3.2 (D-13 L2/L3, 2026-09-28) the listed-rendered
body is method only in the stricter sense too: no commerce, no live money,
no self-triggering, no unrequested writes, and the index never tells the
model to fetch a topic. Topics that moved in-app (`NOT_SERVED_TOPICS`) are
cited by no skill.
"""

from __future__ import annotations

import re
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
    "# Method",
    "# Decision points",
    "# Output shape",
    "# When NOT to use",
    "# Test prompts",
]

# Agent Skills budget (R2 §1.6 / guidance spec §3 L3): the composed
# skill — frontmatter + reference index + body — on ACTIVATION.
SKILL_TOKEN_BUDGET = 5_000
# agentskills.io caps a skill description at 1,024 chars; Claude skills
# allow 1,536. The tighter bound is the one the bundled set meets.
DESCRIPTION_CHAR_CAP = 1_024

# What each skill APPENDED to its composed body before the method-plus-index
# build (the `knowledge:` frontmatter list at commit 47d9492ad). Those
# sections left the body; the reference index is what keeps them reachable,
# so this table is the conservation guard's "before".
APPENDED_SECTIONS_BEFORE = {
    "strategy-creation": (
        "reasoning_principles",
        "composition_mechanics",
        "dsl_syntax",
        "mistakes",
        "tool_usage",
        "universe_selection",
        "pipeline_system",
    ),
    "strategy-fork-and-iterate": (
        "reasoning_principles",
        "composition_mechanics",
        "dsl_syntax",
        "collaboration",
        "mistakes",
    ),
    "backtest-and-analyze": ("reasoning_principles", "mistakes", "trading_domain", "tool_usage"),
    "overfit-check": ("reasoning_principles", "mistakes", "trading_domain"),
    "deploy-and-monitor": ("reasoning_principles", "mistakes", "trading_domain", "tool_usage"),
    "portfolio-review": ("reasoning_principles", "trading_domain"),
    "component-discovery": (
        "composition_mechanics",
        "component_versioning",
        "tool_usage",
        "dsl_syntax",
    ),
    "recover-from-error": ("mistakes", "tool_usage"),
}

# Topics D-13 moved to Keel's in-app chat (`system/chat/`, never vendored,
# never served over MCP — mcp-conversion 05 §3.1). A skill cites none of
# them: its index would name a topic `keel_help` cannot serve, and the
# content is chat conduct or live/plan context that third-party agents
# never get. They are excluded from the conservation guards below — that
# exclusion IS the D-13 decision, not a way to quiet the guard.
NOT_SERVED_TOPICS = frozenset(
    {"collaboration", "strategy_phases", "editor_ui", "component_versioning", "costs_and_fees"}
)

# Per-skill omissions the W3 content accounting (§2.10) assigns elsewhere:
# the section was appended to this skill by a blanket `knowledge:` list, and
# the accounting places its paragraphs at a DIFFERENT skill's step. Each one
# is still named by some skill's index and by `keel_help`'s topic list, which
# the union guard below is what actually proves. Adding a row here is a
# review decision, not a way to quiet the guard.
DELIBERATE_INDEX_OMISSIONS = {
    "backtest-and-analyze": {
        "reasoning_principles": "invariants belong to the compose step (creation/fork)",
    },
    "overfit-check": {
        "mistakes": "structural mistakes are a compose-time read, not a robustness probe",
    },
    "deploy-and-monitor": {
        "reasoning_principles": "invariants belong to the compose step",
        "mistakes": "same — a strategy reaching deploy has already compiled",
        "tool_usage": "its deploy paragraphs were S5-stale; the live facts are in-app "
        "only since D-13 (mcp-conversion 05 §3.1)",
    },
    "portfolio-review": {
        "reasoning_principles": "a read-only survey composes nothing",
    },
    "component-discovery": {
        "dsl_syntax": "discovery ends before any DSL is drafted; creation step 4 owns it",
        "tool_usage": "the discovery rule it cited moved in-app (D-13); the served half "
        "(pipeline completeness, backtest mechanics) belongs to creation and backtest",
    },
    "recover-from-error": {
        "tool_usage": "the retry rule it cited moved in-app (D-13); Steps 5-6 state it as method",
    },
}


# ── Skill .md files exist and are well-formed ─────────────────────────────


class TestSkillFiles:
    def test_portable_layout_is_exactly_eight_skill_dirs(self):
        """Q-0267 / agent-distribution spec 02 R1: skills ship as
        `<name>/SKILL.md` — the portable Agent Skills layout external
        registries read. No flat `.md` strays from the pre-R1 layout."""
        dirs = sorted(
            p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith("__")
        )
        assert dirs == sorted(EXPECTED_SKILLS)
        for d in dirs:
            assert (SKILLS_DIR / d / "SKILL.md").is_file(), f"{d}/SKILL.md missing"
        flat = sorted(p.name for p in SKILLS_DIR.glob("*.md"))
        assert flat == [], f"flat skill files present (pre-R1 layout): {flat}"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_file_exists(self, skill_name):
        skill_file = SKILLS_DIR / skill_name / "SKILL.md"
        assert skill_file.exists(), f"Missing {skill_file}"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_has_valid_frontmatter(self, skill_name):
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        assert content.startswith("---"), f"{skill_name} missing frontmatter"
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"{skill_name} malformed frontmatter"
        fm = yaml.safe_load(parts[1])
        for field in ("name", "description", "references", "tools"):
            assert field in fm, f"{skill_name} frontmatter missing '{field}'"
        assert fm["name"] == skill_name, f"{skill_name} frontmatter name mismatch"
        assert isinstance(fm["references"], list) and len(fm["references"]) >= 1
        assert isinstance(fm["tools"], list) and len(fm["tools"]) >= 1
        # The retired keys carried a second copy of text that now has one
        # owner; a file that keeps one is a drift source, and the loader
        # refuses it.
        for retired in ("trigger", "knowledge"):
            assert retired not in fm, f"{skill_name} frontmatter still carries retired '{retired}:'"
        for entry in fm["references"]:
            assert isinstance(entry, dict), f"{skill_name}: reference {entry!r} not a mapping"
            assert set(entry) == {"step", "topic", "why"}, (
                f"{skill_name}: reference {entry!r} is not {{step, topic, why}}"
            )
            assert entry["why"], f"{skill_name}: reference {entry['topic']} has no 'why'"

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_frontmatter_tools_exist_in_outcome_registry(self, skill_name):
        """Skill tool lists are MCP-facing, so every `keel_*` tool named
        there must exist in the current outcome registry."""
        from keel.tools.outcomes import OUTCOMES, _bootstrap

        _bootstrap()
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        missing = [tool for tool in fm["tools"] if tool not in OUTCOMES]
        assert missing == []

    def test_no_skill_body_inlines_a_reference_section(self):
        """Conservation, the other way round: the method must not have
        quietly re-absorbed a reference. A body carrying a whole knowledge
        section is how the 5k budget was blown before."""
        for skill_name in EXPECTED_SKILLS:
            body = (SKILLS_DIR / skill_name / "SKILL.md").read_text().split("---", 2)[2]
            assert "# Loaded knowledge" not in body, f"{skill_name}: knowledge inlined"
            assert "# Tool Surface Note" not in body, (
                f"{skill_name}: the surface note belongs on the keel_help topic result"
            )

    def test_skill_bodies_do_not_reference_known_stale_mcp_shapes(self):
        """Catch drift where SDK skills accidentally document chat-only
        resources or old argument names instead of the current MCP surface."""
        stale_patterns = {
            "component_name=<name>": "keel_components_get expects name",
            "keel://strategy/list": "use keel_strategy_search instead",
            "keel://deployment/<id>/full": "use keel_live_monitor views instead",
            "summary=<": "keel_strategy_notes_add expects note",
            "share=<id>": "keel_strategy_fork expects source",
            "from_version=": "keel_strategy_diff expects ref_a/ref_b",
            "to_version=": "keel_strategy_diff expects ref_a/ref_b",
        }
        all_text = "\n".join(p.read_text() for p in sorted(SKILLS_DIR.glob("*/SKILL.md")))
        offenders = {
            pattern: reason for pattern, reason in stale_patterns.items() if pattern in all_text
        }
        assert offenders == {}

        deploy_text = (SKILLS_DIR / "deploy-and-monitor" / "SKILL.md").read_text()
        assert "dry_run" not in deploy_text

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_skill_has_required_sections(self, skill_name):
        """Per spec §11.3: Workflow / Common mistakes / Expected output shape
        / When NOT to use / Test prompts — all five, in order."""
        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
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
    def test_description_is_the_one_always_on_field(self, skill_name):
        """`description` is what a router sees before anything loads, so
        it carries what + when + when-not and nothing else. Capped at the
        tighter of the two published bounds (agentskills.io 1,024)."""
        from keel.skills import load_skill

        content = (SKILLS_DIR / skill_name / "SKILL.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        desc = " ".join(fm["description"].split())
        assert len(desc) <= DESCRIPTION_CHAR_CAP, (
            f"{skill_name} description is {len(desc)} chars (cap {DESCRIPTION_CHAR_CAP})"
        )
        # The derived trigger must be real: an index path that renders it
        # (`keel skills list`, the published manifest, the prompt
        # docstring) gets the "when" half, not an empty string.
        trigger = load_skill(skill_name).trigger
        assert trigger, (
            f"{skill_name} description has no 'Use when'/'Use after' sentence, so the "
            f"derived trigger is empty — the index paths would render nothing"
        )
        assert trigger in desc or trigger in fm["description"], (
            f"{skill_name}: the derived trigger is not a slice of the description"
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
        assert sk.references
        assert sk.knowledge  # derived from references
        assert sk.tools
        assert sk.body
        assert "# Method" in sk.body

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_every_reference_topic_is_fetchable(self, skill_name):
        """The index is only "one call away" if the call works. Every
        `references[].topic` must be a topic `keel_help` can serve: a
        bundled doc stem after normalization, a real catalog rule code,
        or the literal `rule:<CODE>` placeholder that stands for the code
        an issue names at runtime."""
        from keel.skills import load_skill
        from keel.tools.outcomes import help as help_tool

        stems = {help_tool._normalize_topic(t) for t in help_tool._list_bundled_topics()}
        assert stems, "the bundled doc index is empty — nothing could resolve"
        sk = load_skill(skill_name)
        for ref in sk.references:
            if ref.topic == "rule:<CODE>":
                continue
            if ref.topic.lower().startswith("rule:"):
                from pipeline_engine.dsl.catalog import RULES

                assert ref.topic.split(":", 1)[1].upper() in RULES, (
                    f"{skill_name}: reference {ref.topic} names no catalog rule"
                )
                continue
            assert help_tool._normalize_topic(ref.topic) in stems, (
                f"{skill_name}: reference topic {ref.topic!r} is not a bundled doc"
            )

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    @pytest.mark.parametrize("profile", ["full", "listed"])
    def test_composed_skill_fits_the_activation_budget(self, skill_name, profile):
        """Guidance spec §5 "skill size": the composed skill is reported
        against the 5k-token aim on BOTH profiles.

        # SEED: in keel/skills/__init__.py's compose_skill, put back the
        # appended corpus — `knowledge_parts.append(load_section(t))` for
        # every reference topic — and strategy-creation blows the budget
        # (dsl_syntax + composition_mechanics alone are ~3.3k tokens).
        """
        from keel.skills import compose_skill

        composed = compose_skill(skill_name, profile=profile)
        # Non-vacuity on a quantity the seed cannot move: the composition
        # actually ran and produced a whole document, header and all.
        assert composed.startswith("---"), f"{skill_name}: no frontmatter emitted"
        assert "# References" in composed and "# Method" in composed
        tokens = len(composed) // 4
        assert tokens <= SKILL_TOKEN_BUDGET, (
            f"{skill_name} ({profile}) composes to {tokens} tokens (budget {SKILL_TOKEN_BUDGET})"
        )

    @pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
    def test_the_index_names_every_section_the_skill_dropped(self, skill_name):
        """The guard the spec asserts: CONSERVATION, not size.

        Before this build each skill APPENDED a fixed set of knowledge
        sections at composition. Those sections left the body; the index
        is what keeps them reachable at the step that needs them. Any
        section a skill used to append and no longer names must be in the
        reviewed omission table with its reason.

        # SEED: delete the `{ step: 4, topic: dsl_syntax, ... }` line from
        # keel/skills/strategy-creation/SKILL.md — this goes red naming it.
        """
        from keel.skills import load_skill

        dropped = APPENDED_SECTIONS_BEFORE[skill_name]
        assert dropped, f"{skill_name}: the pinned before-set is empty — nothing to prove"
        named = set(load_skill(skill_name).knowledge)
        allowed = set(DELIBERATE_INDEX_OMISSIONS.get(skill_name, {}))
        missing = sorted(set(dropped) - named - allowed - NOT_SERVED_TOPICS)
        assert not missing, (
            f"{skill_name} dropped {missing} from its body and its index does not "
            f"name them — that content is unreachable, not moved"
        )

    def test_every_appended_section_survives_somewhere_in_the_index(self):
        """Union conservation — the assertion the omission table cannot
        weaken. Every section ANY skill used to append is named by at
        least one skill's reference index, so no paragraph of the old
        appended corpus became unreachable.

        # SEED: remove `mistakes` from every skill's references — this
        # reds naming it, while the per-skill test above would not.
        """
        from keel.skills import BUNDLED_SKILLS, load_skill

        before = set().union(*(set(v) for v in APPENDED_SECTIONS_BEFORE.values()))
        # Non-vacuity on a quantity no index edit can move: the ten files
        # the old composition appended (W3 §2.10).
        assert len(before) == 10, f"before-set is {len(before)} sections, expected 10"
        named = set().union(*(set(load_skill(n).knowledge) for n in BUNDLED_SKILLS))
        served_before = before - NOT_SERVED_TOPICS
        assert len(served_before) == 8, "the D-13 exclusion should remove exactly two"
        assert len(named) > len(served_before), "the new index names fewer topics than it replaced"
        orphaned = sorted(served_before - named)
        assert not orphaned, f"no skill index names {orphaned} — that corpus is unreachable"

    def test_every_deliberate_omission_is_carried_by_another_skill(self):
        """An omission row is only legitimate while some other skill's
        index still routes to that section. This is what stops the table
        from becoming a place to hide a deletion."""
        from keel.skills import BUNDLED_SKILLS, load_skill

        assert DELIBERATE_INDEX_OMISSIONS, "the omission table is empty — nothing to check"
        for skill_name, rows in DELIBERATE_INDEX_OMISSIONS.items():
            for topic, reason in rows.items():
                assert reason, f"{skill_name}: omission of {topic} has no reason"
                carriers = [
                    other
                    for other in BUNDLED_SKILLS
                    if other != skill_name and topic in load_skill(other).knowledge
                ]
                assert carriers, f"{skill_name} omits {topic!r} and no other skill's index names it"

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

        rows = _json.loads(result.stdout)
        assert len(rows) == 8
        assert {r["name"] for r in rows} == set(EXPECTED_SKILLS)
        for row in rows:
            assert row["description"]
            assert row["trigger"]

    def test_keel_skills_show_strategy_creation_contains_knowledge(self):
        from keel.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--format", "json", "skills", "show", "strategy-creation"])
        assert result.exit_code == 0, result.output
        import json as _json

        body = _json.loads(result.stdout)
        assert body["name"] == "strategy-creation"
        content = body["content"]
        # Frontmatter
        assert content.startswith("---")
        # The reference index, not an appended corpus — a plain list of
        # topics with no instruction to fetch them (05 R-L3).
        assert "# References" in content
        assert "Reference topics this workflow cites (keel_help topics):" in content
        assert "fetch" not in content.split("# References", 1)[1].split("# Method", 1)[0]
        for section in APPENDED_SECTIONS_BEFORE["strategy-creation"]:
            assert f"— `{section}`" in content, (
                f"strategy-creation's index does not list '{section}'"
            )
        assert "## Knowledge:" not in content, "the corpus is appended again"
        # Body sections
        for required in REQUIRED_SECTIONS:
            assert required in content

    def test_keel_skills_show_unknown_errors(self):
        from keel.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["skills", "show", "no-such-skill"])
        assert result.exit_code != 0


# ── agent-surface-cleanup spec 01 §2.7 / §4 #10 ───────────────────────────


def test_the_two_skill_edits_carry_the_new_text_and_not_the_old():
    """The quota bullet names what a refusal actually carries
    (`limit_details`, `resume.verify_call` — review 06 M-5 #5), and the
    creation skill's output item explains the view rather than relaying it
    (head fact 4).

    # SEED (run 2026-09-23, lane L6a): restore "do not retry." at the end
    # of the plan-limit bullet in `keel/skills/recover-from-error/SKILL.md`
    # — this reds. Revert by reversing that edit.
    """
    from keel.skills import load_skill

    recover = (SKILLS_DIR / "recover-from-error" / "SKILL.md").read_text()
    creation = (SKILLS_DIR / "strategy-creation" / "SKILL.md").read_text()
    assert "do not retry" not in recover
    assert "`limit_details`" in recover and "`resume.verify_call`" in recover
    # D-12: the plan-limit bullet reports the limit and the reset; it no
    # longer points at the plan-paths card data or relays a link.
    assert "`limit_view`" not in recover and "action_url" not in recover
    assert "it lifts at the reset" in recover
    assert "relayed as-is" not in creation and "Relay the" not in creation
    assert "explain it in your own words" in creation
    # Non-vacuity, on quantities the seed cannot move: both files are real
    # skills the loader parses.
    for name, text in (("recover-from-error", recover), ("strategy-creation", creation)):
        assert len(text) >= 3000, name
        assert load_skill(name).references, name


def test_strategy_creation_is_the_complete_nl_to_dsl_method():
    """The lane brief's bar #1: an agent that reads only what a host
    delivers still finds the whole construction method in ONE skill —
    thesis → concepts → discovery → skeleton → the dry-run loop reading
    each issue's fix → save → backtest → reasoning — in that order. Stale
    facts review 06 M-3 found stay out.

    # SEED: delete "## Step 7" (the backtest-and-reason step) from
    # `keel/skills/strategy-creation/SKILL.md` — the order arm reds.
    """
    body = (SKILLS_DIR / "strategy-creation" / "SKILL.md").read_text().split("---", 2)[2]
    stages = [
        "## Step 1 — Read the thesis",
        "keel_components_search(query=<concept>)",
        "keel_components_get_many(names=[...])",
        "## Step 4 — Draft from the skeleton",
        "Pipeline([",
        "keel_strategy_compose(dry_run=True",
        "`suggestion` — the fix",
        'keel_help(topic="rule:<CODE>")',
        "## Step 6 — Save",
        "## Step 7",
        "keel_backtest_run(",
        "reason WHY",
    ]
    positions = [body.find(stage) for stage in stages]
    missing = [s for s, p in zip(stages, positions) if p < 0]
    assert not missing, f"the method lost a stage: {missing}"
    assert positions == sorted(positions), dict(zip(stages, positions))
    # Stale facts (review 06 M-3): the broken Path-3 chain, the false
    # "an error blocks a save", and the validator-silence list.
    for stale in (
        "SelectionToSignalConverter",
        "an error-severity issue does",
        "validator is silent on",
        "Four declarations",
    ):
        assert stale not in body, stale
    # Non-vacuity: a real method body, with every stage heading numbered.
    assert len(body) >= 5000
    assert sum(f"## Step {i}" in body for i in range(1, 8)) == 7


# ── mcp-conversion 05 §3.2 / 04 §5.3 — skills are method only (D-13 L2) ──
#
# The listed profile serves each skill as a user-picked MCP prompt and
# through the `keel_help` skills catalogue. What those bodies may not carry:
# commerce (D-12), live money, self-triggering, unrequested writes, a
# pointer that pushes the model to fetch guidance, or a topic that moved
# in-app. The phrase families below are the ones this change removed, so
# each is a regression pin; the corpus-wide families are lane G's G4/G7.

_METHOD_ONLY_FAMILIES = {
    "commerce": re.compile(
        r"upgrad|plan tier|higher plans?|pricing|billing|builder fee|action_url|"
        r"limit_view|see plans|plan_status",
        re.IGNORECASE,
    ),
    "live money": re.compile(
        r"real capital|go(ing)? live|deploy (it )?live|wallet|canary|run forward|"
        r"live readiness|live_readiness",
        re.IGNORECASE,
    ),
    "self-trigger": re.compile(
        r"\bUse after\b|\bwhenever\b|after (three|\d+) (consecutive )?\S*\s?errors|"
        r"triggers twice",
        re.IGNORECASE,
    ),
    "unrequested call": re.compile(
        r"Then `keel_strategy_readiness|next step to offer|first and report its", re.IGNORECASE
    ),
    "fetch pointer": re.compile(r"skill:|call away|fetch only|keel_help\(topic=\"(?!rule:)"),
    "posture": re.compile(r"celebrate", re.IGNORECASE),
}


def _listed_bodies() -> dict[str, str]:
    from keel.mcp.server import LISTED_EXCLUDED_SKILLS
    from keel.skills import BUNDLED_SKILLS, compose_skill

    return {
        name: compose_skill(name, profile="listed")
        for name in BUNDLED_SKILLS
        if name not in LISTED_EXCLUDED_SKILLS
    }


def _method_only_hits(body: str) -> list[str]:
    hits = []
    for family, rx in _METHOD_ONLY_FAMILIES.items():
        for line in body.splitlines():
            m = rx.search(line)
            if m:
                hits.append(f"{family}: [{m.group(0)}] {line.strip()}")
    return hits


def test_listed_skill_bodies_are_method_only():
    """05 §3.2: every listed-rendered skill body — frontmatter (the prompt
    description), reference index and method — carries none of the removed
    families.

    # SEED (run 2026-09-28, lane C): append "run forward small." to the
    # MIXED verdict line in keel/skills/overfit-check/SKILL.md — this reds
    # naming it. Revert by reversing that edit.
    """
    bodies = _listed_bodies()
    # Non-vacuity on quantities no seeded phrase can move: seven served
    # skills, each a whole composed document.
    assert len(bodies) == 7, sorted(bodies)
    for name, body in bodies.items():
        assert body.startswith("---") and "# Method" in body, name
    violations = [
        f"{name}: {hit}" for name, body in bodies.items() for hit in _method_only_hits(body)
    ]
    assert not violations, "listed skill bodies carry non-method content:\n" + "\n".join(violations)


def test_control_the_method_only_scanner_fires_on_the_deploy_skill():
    """CONTROL arm. `deploy-and-monitor` is excluded from listed precisely
    because it is live-money method; the same scanner over its full-profile
    body must fire, or the green above proves nothing."""
    from keel.skills import compose_skill

    hits = _method_only_hits(compose_skill("deploy-and-monitor", profile="full"))
    assert any(h.startswith("live money:") for h in hits), hits


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_no_skill_cites_a_topic_moved_in_app(skill_name):
    """D-13: `collaboration`, `strategy_phases`, `editor_ui`,
    `component_versioning` and `costs_and_fees` are chat-only and never
    served over MCP; a skill index naming one points at nothing.

    # SEED (run 2026-09-28, lane C): add `{ step: 3, topic: collaboration,
    # why: "x" }` to keel/skills/strategy-fork-and-iterate/SKILL.md — the
    # fork arm reds. Revert by reversing that edit.
    """
    from keel.skills import load_skill

    assert NOT_SERVED_TOPICS, "the in-app topic list is empty — nothing to check"
    sk = load_skill(skill_name)
    assert sk.references, f"{skill_name}: empty index — nothing was checked"
    cited = sorted({r.topic for r in sk.references} & NOT_SERVED_TOPICS)
    assert not cited, f"{skill_name} cites in-app-only topics {cited}"


@pytest.mark.parametrize(
    ("skill_name", "call", "consent"),
    [
        ("backtest-and-analyze", "keel_strategy_notes_add(", "When the user asks to save"),
        ("recover-from-error", "`keel_feedback` files", "When the user\nwants it reported"),
        ("strategy-creation", "`keel_strategy_readiness(", "When the user asks where"),
    ],
)
def test_account_writes_and_status_calls_are_user_initiated(skill_name, call, consent):
    """05 §3.2: a strategy-memory write, a feedback report, and an
    ownership-status read happen when the user asks, never as a routine
    closing step. The paragraph that makes the call opens with the user's
    request.

    # SEED (run 2026-09-28, lane C): replace "When the user asks to save or
    # remember the result," with "Then" in backtest-and-analyze Step 5 —
    # the first arm reds. Revert by reversing that edit.
    """
    body = (SKILLS_DIR / skill_name / "SKILL.md").read_text().split("---", 2)[2]
    paragraphs = [p for p in body.split("\n\n") if call in p]
    assert paragraphs, f"{skill_name}: {call!r} not found — the check read nothing"
    for para in paragraphs:
        assert consent in para, f"{skill_name}: {call!r} is not gated on the user:\n{para}"


def test_a_skill_that_fails_to_parse_fails_server_startup(monkeypatch):
    """`_register_skill_prompts` used to catch a skill parse failure and
    start the server with NO prompts — a silent fallback that hid the method
    layer's disappearance behind a healthy server. It now raises, naming the
    broken skill.

    Control arm: the unbroken server registers a prompt for every bundled
    skill (the default full profile excludes none).

    # SEED (run 2026-09-28, lane B2c): restore the try/except-return around
    # `list_skills()` in keel/mcp/server.py — the raise arm reds (the server
    # starts with zero prompts). Revert by reversing that edit.
    """
    import asyncio

    import keel.skills as skills
    from keel.mcp.server import create_server

    for key in ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS"):
        monkeypatch.delenv(key, raising=False)

    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    try:
        prompts = asyncio.run(create_server().list_prompts())
        assert {p.name for p in prompts} >= set(skills.BUNDLED_SKILLS)

        real_read = skills._read_skill_file
        broken = skills.BUNDLED_SKILLS[0]
        monkeypatch.setattr(
            skills,
            "_read_skill_file",
            lambda name: "no frontmatter here" if name == broken else real_read(name),
        )
        skills.list_skills.cache_clear()
        skills.load_skill.cache_clear()
        with pytest.raises(ValueError, match=re.escape(broken)):
            create_server()
    finally:
        monkeypatch.undo()
        skills.list_skills.cache_clear()
        skills.load_skill.cache_clear()
